  function vna_ex1(Action)
% function vna_ex1(Action)  
% Example demonstrating the use of the vna data structure. 
% The contents of this stucture is highly user dependent since the user controls
% what measurements are to be stored in both the file and data returned by the 'get','meas' call. 

% 
% Dick Benson, DSP Technology

  if nargin ==0
     Action='abort';   % prepare for the worst
     % if vna is alive, get the measurement data from it
     if isempty(findobj('type','figure','tag','vna_fig'))
         user_path = vip('get','');
         [file_n,path_n]=uigetfile([userpath, '*.vna'],'Open a vna file',0.5,0.5);
         key = '';
         s=['load ''',path_n,file_n,''' -mat'];   
         eval(s,'Action=''abort'';');
        
         if ~isempty(key) & strcmp(key,'DSPt vna_2 file')  
            Action='init';
         elseif ~isempty(key) & strcmp(key,'DSPt vna_1 file') 
            Action='abort';
            disp('This example requires the SigLab > v2.25 file format.')
         end;
     else
         SLm = vna('get','meas');
         path_n='';
         file_n=' direct from vna';
         Action='init';
     end;
  else
     % retrieve handles
     H = get(gcbf,'userdata');  
     if isempty(H)
        H = get(gcf,'userdata');
     end;   
  end;

  switch Action
     case 'init'
     
         WFIGc = 640;
         HFIGc = 430;
         WPOPc = 90; 
         
         H.path_n = path_n;
         
         H.figure=figure('position',[20 20 WFIGc HFIGc], 'menu','none',...
                         'Name',['Example: vna_ex1.m    file:',path_n,file_n],...
                         'NumberTitle','off','visible','on',...
                         'inverthardcopy','off',...
                         'PaperOrientation','landscape',...  
                         'PaperUnits','normalized',...
                         'PaperpositionMode','Auto',...
                         'resize','off',...
                         'BackingStore','off',...
                         'color',0.5*[1 1 1]); % synch background color
                        
          H.file = uimenu(H.figure,'Label','File');
                   uimenu(H.file,'Label','Open','Callback','vna_ex1(''cb_open'')');
                   uimenu(H.file,'Label','From vna ','Callback','vna_ex1(''cb_vna'')');
     
          H.axis  = axes('Parent',H.figure,'Units','pixels',...
                         'Position',[70+WPOPc,50,WFIGc-90-WPOPc,HFIGc-85],...
                         'Box','on',...
                         'visible','on',...
                         'NextPlot','add',...
                         'DrawMode','fast',...
                         'Color',[0 0 0],...
                         'TickDir','out',...
                         'Xcolor',[1 1 1],...
                         'Ycolor',[1 1 1],...
                         'fontname','arial',...              
                         'FontSize',10); 
                         
          H.xlabel = xlabel('null','color',[1 1 1]);             
          H.ylabel = ylabel('null','color',[1 1 1]); 
          H.title  =  title('null','color',[1 1 1]); 
          
          H.line  = line('parent',H.axis,...
                          'Xdata',[nan,nan],'Ydata',[nan,nan],'clipping','on',...
                          'Color',[0 1 0] ,...
                          'visible','on');
          
          H.funcsel = uicontrol(H.figure,...
                               'Style','Popup','Units','pixels',...
                               'Position',[10,HFIGc-60,WPOPc 25],...
                               'Visible','on',...
                               'BackgroundColor',[0 .75 .75],...
                               'String','null',...
                               'HorizontalAlignment','left',...
                               'CallBack',['vna_ex1(''cb_funcsel'');']);
                              
          H.scmeassel = uicontrol(H.figure,...
                               'Style','Popup','Units','pixels',...
                               'Position',[10, HFIGc-90,WPOPc,25],...
                               'Visible','on',...
                               'BackgroundColor',[0 .75 .75],...
                               'String','scmeas',...
                               'HorizontalAlignment','left',...
                               'CallBack',['vna_ex1(''cb_scmeassel'');']);                      
                                
          H.respsel = uicontrol(H.figure,...
                               'Style','Popup','Units','pixels',...
                               'Position',[10, HFIGc-90,WPOPc,25],...
                               'Visible','off',...
                               'BackgroundColor',[0 .75 .75],...
                               'String','respsel',...
                               'HorizontalAlignment','left',...
                               'CallBack',['vna_ex1(''cb_respsel'');']);
          
          H.refsel = uicontrol(H.figure,...
                              'Style','Popup','Units','pixels',...
                              'Position',[10, HFIGc-120,WPOPc,25],...
                              'Visible','off',...
                              'BackgroundColor',[0 .75 .75],...
                              'String','refsel',...
                              'HorizontalAlignment','left',...
                              'CallBack',['vna_ex1(''cb_refsel'');']);
                              
                              
          H.eu    =  uicontrol(H.figure,...
                              'Style','checkbox','Units','pixels',...
                              'Position',[10, HFIGc-150,WPOPc,25],...
                              'Visible','on',...
                              'BackgroundColor',[0 .75 .75],...
                              'String','Eng Units On',...
                              'HorizontalAlignment','left',...
                              'CallBack',['vna_ex1(''cb_funcsel'');']); 
                              
          H.pwr_cor =  uicontrol(H.figure,...
                              'Style','checkbox','Units','pixels',...
                              'Position',[10, HFIGc-180,WPOPc,25],...
                              'Visible','off',...
                              'BackgroundColor',[0 .75 .75],...
                              'String','Pwr Correct',...
                              'HorizontalAlignment','left',...
                              'CallBack',['vna_ex1(''cb_funcsel'');']);                     
          
          H.ovld    = uicontrol(H.figure,'Style','text','position',[10, 5,3*WPOPc,15],...
                               'string','null');
                              
          set(H.figure,'userdata', H);      % stash handles
          set(H.axis,  'userdata', SLm);    % stash measurement structure
          vna_ex1('load_popups')
          
     case 'load_popups'
          SLm = get(H.axis,'userdata');
          % Identify the measurement functions that are **explicitly** stored ... 
          % and allow selection from this list ... not the whole suite of possible 
          % measurement functions.
          
          H.funcselstr = {};
          H.fieldstr   = {};
          H.crossfunc  = [];   % log functions that are cross channel 
          H.tdfunc     = [];   % log functions that are time domain 
          index        = 1;
          % the filestor.fields enumerates all possible measurement fields
          for i = 1:length(SLm.filestor.fields)
              if SLm.filestor.state{i} ==1
                 % if state is a 1, this function is in the SLm structure
                 H.funcselstr{index}= SLm.filestor.label{i};  % use label for popup
                 H.fieldstr{index}  = SLm.filestor.fields{i}; % register field name
                 
                 % log if function is cross channel
                 if ismember(H.fieldstr{index},{'tdmeas','aspec','fft','acor'});
                    H.crossfunc(index) = 0;
                 else
                    H.crossfunc(index) = 1;
                 end;
                 if ismember(H.fieldstr{index},{'tdmeas','acor','ccor','imp'})
                    H.tdfunc(index)  = 1;
                 else
                     H.tdfunc(index) = 0;
                 end;
                 index=index+1;
              end;
          end;
          set(H.funcsel,'string',H.funcselstr);
          
          % Create list of channels for the single channel measurements 
          % 
          H.scstring = {};
          for i=1:length(SLm.clist)
              H.scstring{i} = sprintf('Chan:%2i',SLm.clist(i));
          end;
          
          % Create list of reference channels for cross function meas
          % and associated response channels
          H.refstring  = {};
          
          H.respstring = [];
         
          for i=1:length(SLm.xcstate.refc)
             H.refstring{i} = sprintf('Ref:%2i',SLm.xcstate.refc(i));
             for k=1:length(SLm.xcstate.resp(SLm.xcstate.refc(i)).r)
                 H.respstring(i).r{k} = sprintf('Resp:%2i',SLm.xcstate.resp(SLm.xcstate.refc(i)).r(k));    
             end;
          end;
          
          % load ref channel popup 
          set(H.refsel,'string',H.refstring);
          % load response channel popup
          set(H.respsel,'string',H.respstring(1).r)
          % load single channel measurement select
          set(H.scmeassel,'string',H.scstring)
          
          
          % show channels that were overloaded 
          if SLm.ovld ==0
            % no overloads
            set(H.ovld,'string','','visible','off');
          else
            ovlstring = ['Overload on channels:',sprintf('%2i', SLm.ovld)];
            set(H.ovld,'string',ovlstring,'visible','on');
          end;
          
          set(H.figure,'userdata', H);    % store for subsequent use
          vna_ex1('cb_funcsel')
          
     case 'cb_funcsel'
         funcnum = get(H.funcsel,'value');
         SLm     = get(H.axis,'userdata');
         
         % show amplitude/power correction button if window selected is not boxcar 
         if strcmp(H.fieldstr{funcnum},'aspec') & (SLm.winsel >1)
            set(H.pwr_cor,'visible','on');
         else
            set(H.pwr_cor,'visible','off');
         end;
         if H.crossfunc(funcnum)
            % cross channel measurement
            set([H.refsel,H.respsel],'visible','on','value',1);
            set([H.scmeassel],'visible','off','value',1);
            vna_ex1('cb_refsel');    % this forces a new plot to be made after the ref stuff has been setup
         else
            % single channel measurement
            set([H.refsel,H.respsel],'visible','off');
            set([H.scmeassel],'visible','on');
            vna_ex1('cb_scmeassel'); % this forces a new plot to be made
         end;
      
     case 'cb_refsel'
          % a change in the reference channel for cross channel measurements
          SLm     = get(H.axis,'userdata');
          refindex   = get(H.refsel,'value');
          refchannum = SLm.xcstate.refc(refindex);
          % load up new response channel selector with precomputed list 
          % of response channels that have this reference channel
          set(H.respsel,'string',H.respstring(refindex).r,'value',1);
          vna_ex1('cb_respsel'); % this forces a new plot to be made .... 
         
     
     case {'cb_scmeassel' 'cb_respsel'}
         % these actions do the "real work" of getting the selected measurement plotted. 
         SLm      = get(H.axis,'userdata');
         funcnum  = get(H.funcsel,'value');
         apply_eu = get(H.eu,'value');
         switch Action
            case 'cb_scmeassel'
              % a single channel measurement 
              channum = SLm.clist(get(H.scmeassel,'value'));
              ydata    = getfield(SLm.scmeas(channum),H.fieldstr{funcnum});         % extract raw measurement data
            case 'cb_respsel'
              % a cross channel measurement, need to identify reference and response channels
              refchan = SLm.xcstate.refc(get(H.refsel,'value'));
              channum = SLm.xcstate.resp(refchan).r(get(H.respsel,'value'));
              ydata    = getfield(SLm.xcmeas(refchan,channum), H.fieldstr{funcnum}); % extract raw measurement data
         end;   
         base_title = [SLm.scmeas(channum).label,' (',H.funcselstr{funcnum},')'];    % most of the title string
         set(H.axis,'ylim',[-inf inf],'xlim',[-inf inf]);  % force autoscalling
         
         if H.tdfunc(funcnum) ==1
            % time domain function
            xlabel='Seconds';
            switch  H.fieldstr{funcnum}
                case 'tdmeas'
                     xdata = SLm.tdxvec;
                     if apply_eu
                        ylabel = SLm.scmeas(channum).eu_string;
                        ydata = ydata*SLm.scmeas(channum).eu_val;
                     else
                        ylabel='Volts';  
                     end;
                     title = base_title;
                    
                case {'acor','ccor'}
                     l  = length(SLm.tdxvec);
                     xdata = (SLm.tdxvec(2)-SLm.tdxvec(1))*(-l/2:1:(l/2-1));
                     
                     switch H.fieldstr{funcnum}
                        case 'ccor'
                             if apply_eu
                                ylabel= [SLm.scmeas(channum).eu_string,'*',SLm.scmeas(refchan).eu_string];
                                ydata = ydata*SLm.scmeas(channum).eu_val*SLm.scmeas(refchan).eu_val;
                             else
                                ylabel= 'Volts^2';  % zzzzz  
                             end;
                             title= [base_title, sprintf(' ref = %2i',refchan)];
                        
                        case 'acor'
                              if apply_eu
                                 ylabel= [SLm.scmeas(channum).eu_string,' ^2'];
                                 ydata = ydata*SLm.scmeas(channum).eu_val;
                              else
                                 ylabel= 'Volts^2';  
                              end;
                              title = base_title;
                    end
                    if SLm.zpad ==1
                       title = [title,' zero padding on'];
                    else
                       title = [title,' CAUTION zero padding was off'];
                    end;
                     
                case 'imp'
                     l  = length(SLm.tdxvec);
                     xdata = (SLm.tdxvec(2)-SLm.tdxvec(1))*(0:1:(l-1));
                     if apply_eu
                        ylabel=[SLm.scmeas(channum).eu_string,'/',SLm.scmeas(refchan).eu_string];
                        ydata = ydata*SLm.scmeas(channum).eu_val/SLm.scmeas(refchan).eu_val;
                     else
                        ylabel='Volts/Volts';  % zzzzz  
                     end;
                     title=[base_title, sprintf(' ref = %2i',refchan)];
            end;
            
         else
            % freq domain function
            xlabel = 'Hertz';
            xdata  = SLm.fdxvec;
            switch  H.fieldstr{funcnum}
                 case 'aspec'
                      % test for power correction of spectrum
                      if get(H.pwr_cor,'value')==1
                         wincor = SLm.wincor;
                         corstr = '  power corrected';
                      else
                         wincor = 1.0;
                         corstr = '  amplitude corrected';
                      end;
                      
                      if apply_eu
                          ydata=10*log10(abs(wincor*ydata)/((SLm.scmeas(channum).eu_val*SLm.scmeas(channum).db_ref)^2));
                          ylabel=sprintf(['dB   (0dB=%1.4f ',SLm.scmeas(channum).eu_string],SLm.scmeas(channum).db_ref);   
                      else
                          
                         ydata=10*log10(abs(wincor*ydata)/(SLm.scmeas(channum).db_ref^2));
                         ylabel=sprintf('dB   (0dB=%1.4f Vrms)',SLm.scmeas(channum).db_ref);   
                      end;
                      title=[base_title,corstr];
                      
                 case 'fft'
                       if apply_eu
                           ydata = abs(ydata)*SLm.scmeas(channum).eu_val;
                           ylabel=['Magnitude in ',SLm.scmeas(channum).eu_string];
                       else
                           ydata = abs(ydata);
                           ylabel= 'Magnitude in Volts';    
                       end;
                       title=base_title;
                       
                 case 'xfer'
                       if apply_eu
                           ydata = 20*log10(abs(ydata*(SLm.scmeas(channum).eu_val/SLm.scmeas(refchan).eu_val)));
                           ylabel=[' dB, 0dB =1.0 ',SLm.scmeas(channum).eu_string,'/',SLm.scmeas(refchan).eu_string];
                       else
                           ydata=20*log10(abs(ydata));
                           ylabel= ' dB, 0dB =1.0 Volts/Volts';
                       end;
                       title=[base_title, sprintf(' ref = %2i',refchan)];
                       
                 case 'coh'
                       % no eu dependence
                       ylabel='';
                       title= [base_title, sprintf(' ref = %2i',refchan)];
                 case 'cspec'
                       if apply_eu
                          ydata=abs(ydata*(SLm.scmeas(channum).eu_val/SLm.scmeas(refchan).eu_val));
                          ylabel=['Magnitude in ',SLm.scmeas(channum).eu_string,'*',SLm.scmeas(refchan).eu_string];
                       else
                          ydata=abs(ydata);  
                          ylabel= 'Magnitude Volts^2';
                       end;
                       title=[base_title, sprintf(' ref = %2i',refchan)];
            end;
         end;
         
         set(H.ylabel,'string',ylabel);
         set(H.xlabel,'string',xlabel);
         set(H.title, 'string',title);
         set(H.line,'xdata',xdata,'ydata',ydata);
         
         
     case 'cb_open'
         
         if isempty(H.path_n)
            path_n = vip('get','');
         else
            path_n = H.path_n;
         end;
          
         [file_n,path_n]=uigetfile([H.path_n, '*.vna'],'Open a vna file',0.5,0.5);
         
         key = '';
         eval(['load ''',path_n,file_n,''' -mat']);
        
         if ~isempty(key) & strcmp(key,'DSPt vna_2 file')  
            H.path_n = path_n;
            set(H.figure,'Name',['Example: vna_ex1.m file:',path_n,file_n],...
                'userdata',H);
            set(H.axis,'userdata',SLm);
            vna_ex1('load_popups');
            
         elseif ~isempty(key) & strcmp(key,'DSPt vna_1 file') 
            disp('This example requires the SigLab > v2.25 file format.')
         end;
         
         
     case 'cb_vna'
         if isempty(findobj('type','figure','tag','vna_fig'))
            disp('vna is not running, cannot load')
         else
            vna('stop');
            SLm = vna('get','meas');
            path_n='';
            file_n=' direct from vna';
            H.path_n = path_n;
            set(H.figure,'Name',['Example: vna_ex1.m  file:',path_n,file_n],'userdata',H);
            set(H.axis,'userdata',SLm);
            vna_ex1('load_popups');
         end;
         
          
     case 'abort'
         disp('vna was not running and no valid file was selected');
  
  otherwise
     disp([Action,' not recognized in vna_ex1.m']);
  
  end;

% end function
