  function cdx()
% function cdx()
% A directory change utility.
% If a file is picked with a 3 letter extension beginning in 'V', a virtual 
% instrument is invoked, with the selected file as the input. 
% Dick Benson DSP Technology

   [file_n,path_n]=uigetfile('*.*','Change Directory... Select a Dir, then a File, Click OK',0.5,0.5);
   if file_n ~=0
      eval(['cd ',path_n(1:(length(path_n)-1))]);
      cd
      % check file extension .... if a vi.... fire it up
      
      point=findstr('.',file_n);
      if ~isempty(point)
         % get extension, since extension indicates which VI (if any) 
         % generated the file.
         ext = upper(file_n(point+1:length(file_n)));
         if length(ext)==3 & strcmp(ext(1),'V')
            
            % see if VI is running
            [stat,owners]=hw_stat('owners');
            running = 0;
            if ~isempty(owners)
              for i=1:2
                 if length(owners(i,:))>3
                   if strcmp(upper(owners(i,1:3)),ext)
                      running=1;
                   end;
                 end;
              end;
            end;
            if running
               eval([ext,'(','''open',''',''',path_n,''',','''',file_n,'''',')'])
            else
               eval([ext,'(','''init',''',''',path_n,''',','''',file_n,'''',')'])
            end;
         end;
      end;
   else
      disp('No file selected. You must select a file to change the MATLAB directory.');
      disp('If the file has a 3 character extension beginning in V, the corresponding ');
      disp('SigLab virtual instrument will be invoked using that file to restore the state');
   end;
% end function cdx 
