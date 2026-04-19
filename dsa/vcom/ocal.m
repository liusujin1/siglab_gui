%ocal                                                           2-Aug-94
function ocal(chan)     % calibrates output channel chan

Npts = 2048;            % Frame size
TestF1 = 10;            % 10 Hz (4 cycle sine wave at Npts = 2048)
TossA = 50;             % Wait for offset settling
TestV1 = 8;             % Offset gain cal test voltage
FC = 3.14159;           % special code for writing eerom factory cal

[drive,ppath]=pathfind('vbin');   
[nIn,nOut] = siglab('IOinit',[drive,ppath,'\siglab.out']);
pause(1);
if nIn == 0 disp('No input channels found, check power switch');
else
  fprintf(1,'Enter channel number to calibrate\n');
  fprintf(1,'or press ENTER to calibrate all output channels (1 to %d)\n',nOut);
  k = input(' ---> ');
  if length(k) == 0   chA = 1;  chB = nOut;  else  chA = k;  chB = k;  end;
  chans = chA : chB;
  n = length(chans);    % number of channels to calibrate
  cf = [ones(17,n); zeros(1,n)];
  for ch = chans
    fprintf(1,'Connect output channel %d to input channel %d\n',ch,ch);
    siglab('SendCal','Chan',ch,'Output',cf(:,1));   % reset output cal factors
    siglab('OutSine',ch,TestF1);
    siglab('OutLevel',ch,0,'Offset',TestV1);
  end;
  
  siglab('InpSet',chans,Npts,'BW',2000);         % use 2 kHz bandwidth
  siglab('InpGain',chans,10);
  siglab('Trigger',1,'FreeRun');
  input('Press enter when ready');
  fprintf(1,' Calibrating');  for k=1:30 fprintf(1,'.'); end;
  v1 = vavg(chans,Npts,TossA); 
  for ch = chans  siglab('OutLevel',ch,0); end;
  siglab('InpGain',chans,0.312);
  v0 = vavg(chans,Npts,TossA); 
  cf(17,:) = TestV1 ./ (v1 - v0);
  cf(18,:) = -v0;
  for ch = chans  siglab('SendCal','Chan',ch,'Output',cf(:,ch-chA+1)); end;
  v0 = 10;  v1 = 20;
  for k = 1:16
    for ch = chans  siglab('OutLevel',ch,v0); end;
    siglab('InpGain',chans,v1);
    cf(k,:) = .5 * pi * vavg(chans,-Npts,1+k/6) / v0;
    v0 = v0/2;
    if v1 > .1  v1 = v1/2;  end;   % no more sensitive than 78 mV range
  end;
  for k = 1:n   % for each channel
    if cf(16,k) > 1.32  cf(16,k) = 1.32; end;  % last value could be a bit wild
    siglab('SendCal','Chan',k+chA-1,'Output',cf(:,k));
  end;

  f = 1;        % send table of results to command window
  fprintf(f,'\n\nLevel (mV)     ');
  for ch=chans fprintf(f,'Chan%d      ',ch); end;
  fprintf(f,'\n');  vr = 10000;
  for k = 1:16
    fprintf(f,' %8.1f     ',vr);
    for j=1:n fprintf(f,'%7.5f    ',cf(k,j)); end;  fprintf(f,'\n');
    vr = vr/2;
  end;
  fprintf(f,'ODAC gain     ');
    for j=1:n fprintf(f,'%7.5f    ',cf(17,j)); end;
  fprintf(f,'\nmV Offset     ');
    for j=1:n fprintf(f,'%7.2f    ',1000*cf(18,j)); end;

  if max(abs(cf(18,:))) > 0.320     % can't encode offsets more than .32767V
     fprintf(f,'\n ********* Error: Output offset greater than 320 mV');
  end;  
  cf(18,:) = ones(1,n);
  if max(max(abs(1-cf))) > 0.320
    fprintf(f,'\n ********* Error: Gain error greater than 32 percent');
  end;
  
  sv = 0;
  while sv == 0 
    fprintf(1,'\n\n Enter  1  to save above values to EEROM\n');
    fprintf(1,'        2  to restore the factory calibration factors\n');
    fprintf(1,'        3  to save uncalibrated factors to EEROM\n');
    fprintf(1,'        4  to skip writing of the EEROM\n');
    sv = input(' ---> ');
    if sv == 1      siglab('SendCal','SaveO');    disp('user cal written');
    elseif sv == 2  siglab('SendCal','RestoreO'); disp('factory cal restored');
    elseif sv == FC  siglab('SendCal','FactoryO');
                     siglab('SendCal','SaveO');
                     disp('factory and user cals written');
    elseif sv == 3  for ch = chans
                      siglab('SendCal','Chan',ch,'Output',[ones(17,1); 0]);
                    end;
                    siglab('SendCal','SaveO');  disp('uncalibrated');
    elseif sv == 4  disp('EEROM not written');
    else sv = 0;    % indicate that no legal choice was entered
    end;            % end if sv == 1
  end;              % end while sv == 0

end; % end if nIn == 0


